import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { NotFoundPage, UnreachablePage, useServerReachability } from "@/components/errors";
import { StatusBar } from "@/components/StatusBar";
import { Toasts } from "@/components/ui/Toasts";
import { TooltipProvider } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { isDashLocation, parseDashParams } from "@/lib/dash";
import { trackEngineerStatus } from "@/lib/engineerStatus";
import { isEngineerLocation } from "@/lib/engineerRoute";
import { isOverlayLocation, parseOverlayRoute } from "@/lib/overlay";
import { parseAnalysisParams, parseHash, type Route } from "@/lib/router";
import { useTelemetry } from "@/store/telemetry";

// Caught from app start: the server sends it once per socket connect, before
// any lazily loaded view could subscribe (lib/engineerStatus).
trackEngineerStatus();

// Every view is its own chunk (#33): an OBS overlay source or a phone on
// /dash downloads only that view's code — in particular not ECharts, which
// only the Analysis/Survey/Tracks maps use.
const AnalysisView = lazy(() => import("@/views/AnalysisView").then((m) => ({ default: m.AnalysisView })));
const DashView = lazy(() => import("@/views/DashView").then((m) => ({ default: m.DashView })));
const EngineerView = lazy(() => import("@/views/EngineerView").then((m) => ({ default: m.EngineerView })));
const LiveView = lazy(() => import("@/views/LiveView").then((m) => ({ default: m.LiveView })));
const OverlaysView = lazy(() => import("@/views/OverlaysView").then((m) => ({ default: m.OverlaysView })));
const OverlayView = lazy(() => import("@/views/OverlayView").then((m) => ({ default: m.OverlayView })));
const SettingsView = lazy(() => import("@/views/SettingsView").then((m) => ({ default: m.SettingsView })));
const SessionsView = lazy(() => import("@/views/SessionsView").then((m) => ({ default: m.SessionsView })));
const SurveyView = lazy(() => import("@/views/SurveyView").then((m) => ({ default: m.SurveyView })));
const TracksView = lazy(() => import("@/views/TracksView").then((m) => ({ default: m.TracksView })));

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  return route;
}

// A deep link to a session this logger doesn't have (deleted, or copied from
// another installation) is a 404, not an empty Analysis view. Null while
// unknown — a failed lookup never blocks the view.
function useMissingSession(sessionId: number | undefined): number | null {
  const [missing, setMissing] = useState<number | null>(null);
  useEffect(() => {
    setMissing(null);
    if (sessionId == null) return;
    let alive = true;
    api
      .sessions()
      .then((list) => {
        if (alive && !list.some((s) => s.id === sessionId)) setMissing(sessionId);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [sessionId]);
  return missing;
}

export default function App() {
  const route = useRoute();
  const connect = useTelemetry((s) => s.connect);
  const wsConnected = useTelemetry((s) => s.wsConnected);
  const chromeless =
    isOverlayLocation(window.location) ||
    isDashLocation(window.location) ||
    isEngineerLocation(window.location);
  // The unreachable page belongs to the app shell; the chrome-less pages
  // keep their own quiet reconnect.
  const reach = useServerReachability(wsConnected, !chromeless);

  useEffect(() => connect(), [connect]);

  // Selection handed to Analysis via deep link / cross-view navigation.
  // Keyed on the serialized params so pasting a new URL re-applies it.
  const analysisParams = route.params.toString();
  const analysisRequest = useMemo(
    () => parseAnalysisParams(new URLSearchParams(analysisParams)),
    [analysisParams],
  );
  const missingSession = useMissingSession(
    !chromeless && route.view === "analysis" ? analysisRequest.session : undefined,
  );

  // Chrome-less deep links render nothing while their chunk loads — a
  // spinner would flash inside an OBS capture.
  if (isOverlayLocation(window.location)) {
    return (
      <Suspense fallback={null}>
        <OverlayView route={parseOverlayRoute(window.location)} />
      </Suspense>
    );
  }
  if (isDashLocation(window.location)) {
    return (
      <Suspense fallback={null}>
        <DashView params={parseDashParams(window.location)} />
      </Suspense>
    );
  }
  if (isEngineerLocation(window.location)) {
    return (
      <Suspense fallback={null}>
        <EngineerView />
      </Suspense>
    );
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-full flex-col">
        <StatusBar view={route.view} />
        <main className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5">
          <Suspense
            fallback={<div className="p-6 text-sm text-ink-dim">Loading…</div>}
          >
            {/* The server is gone: no view can show anything true, so the
                page says so instead (the StatusBar stays for navigation). */}
            {reach.unreachable ? (
              <UnreachablePage reach={reach} />
            ) : (
              <>
                {route.view === "notfound" && <NotFoundPage />}
                {route.view === "live" && <LiveView />}
                {route.view === "analysis" &&
                  (missingSession != null ? (
                    <NotFoundPage
                      body={`Session #${missingSession} isn’t in this logger’s database — it may have been deleted, or the link came from another installation. Lap links only work on the logger that recorded them.`}
                      diag={`${window.location.hash} · no session ${missingSession}`}
                    />
                  ) : (
                    <AnalysisView request={analysisRequest} />
                  ))}
                {route.view === "sessions" && <SessionsView subTab={route.params.get("sub") === "bests" ? "bests" : "sessions"} />}
                {route.view === "survey" && <SurveyView />}
                {route.view === "tracks" && <TracksView />}
                {route.view === "overlays" && <OverlaysView />}
                {route.view === "settings" && <SettingsView section={route.params.get("section")} />}
              </>
            )}
          </Suspense>
        </main>
        <Toasts />
      </div>
    </TooltipProvider>
  );
}
