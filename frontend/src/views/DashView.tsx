// Full-screen driver dashboard for a tablet or phone beside the wheel.
// Renders a built-in preset or a saved dash layout on the shared grid. Live
// data shows straight away; the only first-run step is the voice button,
// because browsers need a tap before they may speak.

import { useCallback, useEffect, useRef, useState } from "react";
import { CalloutPill } from "@/components/CalloutPill";
import { GridRenderer } from "@/components/GridRenderer";
import { Select } from "@/components/ui/Select";
import { computeAlerts } from "@/lib/alerts";
import { api } from "@/lib/api";
import { dashSearch, withoutAlertsRow, type DashParams } from "@/lib/dash";
import { DASH_PRESETS, DEFAULT_DASH_PRESET } from "@/lib/dashPresets";
import { formatAge, voiceState } from "@/lib/engineerState";
import { normalizeLayout, type LayoutConfig, type LayoutSummary } from "@/lib/layout";
import { projectStrategy } from "@/lib/strategy";
import { useLiveFrame } from "@/lib/useLiveFrame";
import { useNow } from "@/lib/useNow";
import { useVoiceClient } from "@/lib/useVoiceClient";
import { clientId, useEngineer } from "@/store/engineer";

// The bar steps back while driving and comes back on any touch.
const BAR_FADE_MS = 5000;

/** A preset key, or a saved layout's id/name as ?layout= takes it. */
type Choice = { preset: string; layout?: undefined } | { layout: string; preset?: undefined };

export function DashView({ params }: { params: DashParams }) {
  // The dashboard is the primary driver-facing surface, so it is one of the
  // two pages allowed to speak (see lib/useVoiceClient).
  useVoiceClient("dash");

  useEffect(() => {
    document.body.classList.add("overlay-page");
    return () => document.body.classList.remove("overlay-page");
  }, []);

  const [choice, setChoice] = useState<Choice>(() =>
    params.layout
      ? { layout: params.layout }
      : { preset: DASH_PRESETS[params.preset ?? ""] ? params.preset! : DEFAULT_DASH_PRESET },
  );
  const [saved, setSaved] = useState<LayoutSummary[]>([]);
  const [serverLayout, setServerLayout] = useState<LayoutConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.layouts
      .list()
      .then((all) => {
        if (!cancelled) setSaved(all.filter((l) => l.kind === "dash"));
      })
      .catch(() => {}); // the presets still work; "My layouts" just stays empty
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setServerLayout(null);
    setError(null);
    if (!choice.layout) return;
    let cancelled = false;
    api.layouts
      .get(choice.layout)
      .then((l) => {
        if (!cancelled) setServerLayout(normalizeLayout(l.config));
      })
      .catch(() => {
        if (!cancelled) setError(`Layout "${choice.layout}" not found`);
      });
    return () => {
      cancelled = true;
    };
  }, [choice.layout]);

  // Keep the URL in step so a refresh or a bookmark lands on the same screen.
  // replaceState, not a hash change: the choice is not worth a Back step.
  const choose = (next: Choice) => {
    setChoice(next);
    const search = dashSearch(next, params.demo);
    const url = window.location.hash.startsWith("#/dash")
      ? `${window.location.pathname}#/dash${search}`
      : `/dash${search}`;
    window.history.replaceState(null, "", url);
  };

  const layout =
    choice.layout != null ? serverLayout : DASH_PRESETS[choice.preset].layout;
  // Force the dashboard shape regardless of source: fill the screen, dark
  // page, and no alerts row — the banner below already carries alerts.
  const resolved: LayoutConfig | null = layout
    ? withoutAlertsRow({ ...layout, size: null, page: "dark" })
    : null;

  const demo = params.demo || (resolved?.demo ?? false);
  const { frame, laps, placeholder } = useLiveFrame(demo);
  const status = placeholder ? "placeholder" : frame ? "live" : "waiting";

  const alerts = frame ? computeAlerts(frame, laps) : [];
  const topAlert = alerts[0] ?? null;
  const strategy = frame && topAlert ? projectStrategy(frame, laps) : null;

  // Every screen a tap can reach, in segmented-control order.
  const cycle: Choice[] = [
    ...Object.keys(DASH_PRESETS).map((preset) => ({ preset })),
    ...saved.map((l) => ({ layout: String(l.id) })),
  ];
  const savedMatch = choice.layout
    ? saved.find((l) => String(l.id) === choice.layout || l.name === choice.layout)
    : undefined;
  const cycleIndex = cycle.findIndex((c) =>
    c.preset ? c.preset === choice.preset : savedMatch && c.layout === String(savedMatch.id),
  );

  const bar = useBarFade();

  return (
    <div
      className="flex h-full w-full flex-col gap-2.5 p-3 font-tabular"
      onPointerDownCapture={bar.touch}
    >
      <TopBar
        faded={bar.faded}
        frame={frame}
        status={status}
        choice={choice}
        saved={saved}
        savedValue={savedMatch ? String(savedMatch.id) : (choice.layout ?? "")}
        onChoose={choose}
      />

      {/* Alerts get the full width above the tiles rather than a grid cell:
          low fuel, pit window, overheating and oil pressure are the things
          that must be seen without looking for them. */}
      {topAlert && (
        <div
          className={`flex flex-shrink-0 flex-wrap items-center gap-3 rounded-panel border px-4 py-2.5 ${
            topAlert.severity === "critical"
              ? "border-brake/50 bg-brake/10"
              : "border-warn/50 bg-warn/[0.09]"
          }`}
        >
          <span
            className={`h-2 w-2 animate-pulse-dot rounded-full ${
              topAlert.severity === "critical" ? "bg-brake" : "bg-warn"
            }`}
          />
          <span
            className={`text-[15px] font-semibold uppercase tracking-[0.08em] ${
              topAlert.severity === "critical" ? "text-brake" : "text-warn"
            }`}
          >
            {topAlert.message}
          </span>
          <span className="ml-auto text-[11px] text-ink-faint">
            {[
              alerts.length > 1 ? `+${alerts.length - 1} more` : "",
              strategy ? `fuel for ${strategy.lapsToEmpty.toFixed(1)} laps` : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
      )}

      {/* Tapping the canvas switches layout — but a tap that only brings the
          faded bar back must not also change the screen under the driver. */}
      <div
        className="relative min-h-0 flex-1 cursor-pointer"
        onClick={() => {
          if (bar.wasFaded() || cycle.length < 2) return;
          choose(cycle[(cycleIndex + 1) % cycle.length]);
        }}
      >
        {error ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-dim">
            {error} — save it in Overlays first.
          </div>
        ) : !resolved ? null : !frame ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-ink-dim">
            <div>Waiting for telemetry…</div>
            <div className="text-xs">
              add <code className="text-ink">?demo=1</code> to preview with placeholder data
            </div>
          </div>
        ) : (
          <GridRenderer layout={resolved} frame={frame} laps={laps} />
        )}
      </div>

      <CaptionStrip />
    </div>
  );
}

// Fades the top bar after BAR_FADE_MS without a touch. `wasFaded` answers,
// for the click that follows a pointerdown, whether that touch only woke it.
function useBarFade() {
  const [faded, setFaded] = useState(false);
  const fadedRef = useRef(false);
  const wokeRef = useRef(false);
  const timer = useRef(0);

  const arm = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      fadedRef.current = true;
      setFaded(true);
    }, BAR_FADE_MS);
  }, []);

  useEffect(() => {
    arm();
    return () => window.clearTimeout(timer.current);
  }, [arm]);

  const touch = useCallback(() => {
    wokeRef.current = fadedRef.current;
    fadedRef.current = false;
    setFaded(false);
    arm();
  }, [arm]);

  return { faded, touch, wasFaded: () => wokeRef.current };
}

// Screen Wake Lock: only in secure contexts (HTTPS or localhost), so on a
// plain-HTTP LAN address it is simply unavailable. The browser drops the lock
// whenever the tab is hidden; it is taken again on return if still wanted.
function useWakeLock() {
  const supported = typeof navigator !== "undefined" && "wakeLock" in navigator;
  const [held, setHeld] = useState(false);
  const wanted = useRef(false);
  const sentinel = useRef<WakeLockSentinel | null>(null);

  const acquire = useCallback(async () => {
    wanted.current = true;
    if (!supported || sentinel.current) return;
    try {
      const lock = await navigator.wakeLock.request("screen");
      sentinel.current = lock;
      setHeld(true);
      lock.addEventListener("release", () => {
        sentinel.current = null;
        setHeld(false);
      });
    } catch {
      setHeld(false); // refused (battery saver, hidden tab) — the button stays off
    }
  }, [supported]);

  const release = useCallback(() => {
    wanted.current = false;
    void sentinel.current?.release();
  }, []);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && wanted.current) void acquire();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      void sentinel.current?.release();
    };
  }, [acquire]);

  return { supported, held, acquire, release };
}

const BAR_CONTROL =
  "flex h-9 items-center gap-2 rounded-md border px-3 text-xs transition-colors";

function TopBar({
  faded,
  frame,
  status,
  choice,
  saved,
  savedValue,
  onChoose,
}: {
  faded: boolean;
  frame: ReturnType<typeof useLiveFrame>["frame"];
  status: "live" | "placeholder" | "waiting";
  choice: Choice;
  saved: LayoutSummary[];
  savedValue: string;
  onChoose: (c: Choice) => void;
}) {
  const s = useEngineer();
  const wake = useWakeLock();
  const state = voiceState({ ...s, clientId: clientId() });

  const title = frame ? [frame.car_name, frame.track_name].filter(Boolean).join(" · ") : "";
  const meta = frame
    ? [
        frame.current_lap > 0 ? `lap ${frame.current_lap}` : "",
        frame.total_positions > 0 ? `P${frame.position}/${frame.total_positions}` : "",
      ]
        .filter(Boolean)
        .join(" · ")
    : "no telemetry";

  // The saved layout named in ?layout= may be missing from the list (renamed,
  // or not a dash layout); show it anyway so the control says what is on.
  const savedOptions = saved.map((l) => ({ value: String(l.id), label: l.name }));
  if (choice.layout && !savedOptions.some((o) => o.value === savedValue)) {
    savedOptions.push({ value: savedValue, label: savedValue });
  }

  return (
    <div
      className={`flex flex-shrink-0 flex-wrap items-center gap-2.5 transition-opacity duration-500 ${
        faded ? "opacity-35" : ""
      }`}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <span
          title={status}
          className={`h-[7px] w-[7px] shrink-0 rounded-full ${
            status === "live" ? "bg-throttle" : status === "placeholder" ? "bg-warn" : "bg-brake"
          }`}
        />
        {title && <span className="truncate text-xs">{title}</span>}
        <span className="text-[11px] text-ink-faint">{meta}</span>
      </span>

      <div
        className="ml-auto inline-flex overflow-hidden rounded-md border border-edge"
        role="radiogroup"
        aria-label="Dash layout"
      >
        {Object.entries(DASH_PRESETS).map(([key, p]) => (
          <button
            key={key}
            role="radio"
            aria-checked={choice.preset === key}
            className={`h-9 px-3.5 text-xs transition-colors ${
              choice.preset === key
                ? "bg-accent/16 text-accent-300"
                : "text-ink-faint hover:text-ink"
            }`}
            onClick={() => onChoose({ preset: key })}
          >
            {p.label}
          </button>
        ))}
        {savedOptions.length > 0 ? (
          <Select
            variant="segment"
            ariaLabel="My layouts"
            placeholder="My layouts"
            className="h-9 px-3.5 text-xs"
            value={choice.layout ? savedValue : ""}
            onValueChange={(layout) => onChoose({ layout })}
            options={savedOptions}
          />
        ) : (
          <button
            disabled
            className="h-9 px-3.5 text-xs text-ink-ghost"
            title="No saved dash layouts yet — build one in Overlays as a Driver dash"
          >
            My layouts ▾
          </button>
        )}
      </div>

      {state === "speaking" ? (
        <a
          href="/engineer"
          className={`${BAR_CONTROL} border-throttle/50 text-throttle hover:border-throttle`}
          title="Race Engineer settings"
        >
          <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-throttle" />
          Voice · speaking here
        </a>
      ) : state === "other" ? (
        <a
          href="/engineer"
          className={`${BAR_CONTROL} border-warn/50 text-warn hover:border-warn`}
          title="Another device has the voice — take it over from the Race Engineer page"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-warn" />
          Voice · another device
        </a>
      ) : state === "error" ? (
        <a
          href="/engineer"
          className={`${BAR_CONTROL} border-brake/50 text-brake hover:border-brake`}
          title={s.speechError ?? "This browser can't speak"}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-brake" />
          Voice · not working
        </a>
      ) : (
        <button
          className={`${BAR_CONTROL} border-accent font-semibold text-accent hover:bg-accent/12`}
          onClick={() => {
            // Speech first: its unlock has to happen inside the tap. The same
            // tap is the gesture the wake lock wants too.
            void s.enableVoice();
            void wake.acquire();
          }}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          Turn on voice
        </button>
      )}

      <button
        className={`${BAR_CONTROL} ${
          wake.held
            ? "border-accent/50 text-accent-300"
            : "border-edge text-ink-muted hover:border-edge-bright hover:text-ink"
        } disabled:cursor-not-allowed disabled:opacity-45`}
        disabled={!wake.supported}
        aria-pressed={wake.held}
        title={
          !wake.supported
            ? "This browser can only keep the screen on over HTTPS or localhost"
            : wake.held
              ? "Screen stays on — tap to let it sleep"
              : "Keep the screen on"
        }
        onClick={() => (wake.held ? wake.release() : void wake.acquire())}
      >
        {wake.held && <span className="h-1.5 w-1.5 rounded-full bg-accent" />}
        Awake
      </button>
      <button
        className={`${BAR_CONTROL} border-edge text-ink-muted hover:border-edge-bright hover:text-ink disabled:opacity-45`}
        aria-label="Full screen"
        title="Full screen"
        disabled={!document.fullscreenEnabled}
        onClick={() => {
          if (document.fullscreenElement) void document.exitFullscreen();
          else void document.documentElement.requestFullscreen?.();
        }}
      >
        ⤢
      </button>
      {/* /#/live (not #/live): /dash may be a path, where a bare hash change
          would still match isDashLocation and go nowhere. */}
      <a
        href="/#/live"
        className={`${BAR_CONTROL} border-edge text-ink-muted hover:border-edge-bright hover:text-ink`}
      >
        Exit
      </a>
    </div>
  );
}

// The engineer's last line, pinned under the grid. Rendered from the callout
// feed rather than from what was spoken, so it is also the fallback when this
// device can't (or doesn't) speak.
function CaptionStrip() {
  const captions = useEngineer((s) => s.captions);
  const latest = useEngineer((s) => s.history[0] ?? null);
  const receipt = useEngineer((s) => (latest ? s.receipts[latest.id] : undefined));
  const now = useNow();

  if (!captions) return null;
  const critical = (latest?.priority ?? 0) >= 90;
  return (
    <div
      className={`flex flex-shrink-0 items-center gap-3 rounded-panel bg-panel px-4 py-2.5 ${
        critical ? "ring-1 ring-brake/60" : "ring-1 ring-hairline"
      }`}
    >
      {latest ? (
        <>
          <CalloutPill category={latest.category} />
          <span className="min-w-0 truncate text-[17px]">“{latest.text}”</span>
        </>
      ) : (
        <span className="text-[13px] text-ink-faint">The engineer’s callouts show here.</span>
      )}
      <span className="ml-auto shrink-0 text-[11px] text-ink-faint">
        Race Engineer{receipt ? ` · ${formatAge(now - receipt.receivedAt)}` : ""}
      </span>
    </div>
  );
}
