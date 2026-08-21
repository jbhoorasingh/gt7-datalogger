// Configurable overlay / dashboard. Two modes:
//  - legacy: the whole config is in the URL params (/overlay?w=gear,speed&…),
//    rendered with the original strip/stack/grid flow so existing OBS browser
//    sources are pixel-identical;
//  - server: /overlay?layout=<name-or-id> fetches a saved v2 layout and
//    renders it on the free-placement grid (GridRenderer).

import { useEffect, useState, type CSSProperties } from "react";
import { GridRenderer } from "@/components/GridRenderer";
import { api } from "@/lib/api";
import { normalizeLayout, type LayoutConfig } from "@/lib/layout";
import type { OverlayConfig, OverlayPage, OverlayRoute, WidgetId } from "@/lib/overlay";
import { useLiveFrame } from "@/lib/useLiveFrame";
import type { LapSummary, LiveFrame } from "@/lib/types";
import { WIDGET_META } from "@/lib/widgetMeta";
import { WIDGET_COMPONENTS } from "@/lib/widgetRegistry";

function pageBodyClass(page: OverlayPage): string {
  return page === "green" ? "overlay-green" : page === "dark" ? "overlay-page" : "overlay";
}

function usePageClass(pageClass: string) {
  useEffect(() => {
    document.body.classList.add(pageClass);
    return () => {
      document.body.classList.remove("overlay", "overlay-page", "overlay-green");
    };
  }, [pageClass]);
}

function PlaceholderBadge() {
  return (
    <div className="pointer-events-none fixed right-2 top-2 rounded border border-warn/40 bg-black/60 px-1.5 py-0.5 text-[9px] uppercase tracking-widest text-warn">
      placeholder
    </div>
  );
}

export function OverlayView({ route }: { route: OverlayRoute }) {
  if (route.kind === "server") {
    return <ServerOverlay layoutRef={route.ref} demoOverride={route.demo} />;
  }
  return <LegacyOverlay config={route.config} />;
}

// --- server-saved layouts ---------------------------------------------------

function ServerOverlay({
  layoutRef,
  demoOverride,
}: {
  layoutRef: string;
  demoOverride: boolean;
}) {
  const [layout, setLayout] = useState<LayoutConfig | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLayout(null);
    setError(null);
    api.layouts
      .get(layoutRef)
      .then((l) => {
        if (!cancelled) setLayout(normalizeLayout(l.config));
      })
      .catch(() => {
        if (!cancelled) setError(`Layout "${layoutRef}" not found`);
      });
    return () => {
      cancelled = true;
    };
  }, [layoutRef]);

  const demo = demoOverride || (layout?.demo ?? false);
  const { frame, laps, placeholder } = useLiveFrame(demo);
  usePageClass(pageBodyClass(layout?.page ?? "transparent"));

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-dim">
        {error} — save it in the Admin builder first.
      </div>
    );
  }
  if (!layout) return null;
  if (!frame) {
    return layout.page === "dark" ? (
      <div className="flex h-full items-center justify-center text-sm text-ink-dim">
        Waiting for telemetry…
      </div>
    ) : null;
  }

  return (
    <>
      <GridRenderer layout={layout} frame={frame} laps={laps} />
      {placeholder && <PlaceholderBadge />}
    </>
  );
}

// --- legacy URL-param overlays ----------------------------------------------

function LegacyOverlay({ config }: { config: OverlayConfig }) {
  const { frame, laps, placeholder } = useLiveFrame(config.demo);
  const gridPage = config.layout === "grid";
  usePageClass(pageBodyClass(config.page));

  if (!frame) {
    return gridPage ? (
      <div className="flex h-full items-center justify-center text-sm text-ink-dim">
        Waiting for telemetry…
      </div>
    ) : null;
  }

  const bare = config.bg === 0; // no box at all: floating widgets
  // On a chroma-key page translucent cards let green bleed through and key
  // out badly — force them opaque.
  const cardAlpha = config.page === "green" ? 1 : config.bg / 100;
  const card: CSSProperties = bare
    ? {}
    : { backgroundColor: `rgba(8, 10, 14, ${cardAlpha})` };
  // Explicit canvas: render at exactly size.width x size.height so the page
  // matches the OBS browser-source dimensions. The global zoom scales content,
  // so the un-zoomed box is size/scale to land on the exact pixel size.
  const frameStyle: CSSProperties = {
    zoom: config.scale,
    padding: `${config.padY}px ${config.padX}px`,
    ...(config.size
      ? {
          width: config.size.width / config.scale,
          height: config.size.height / config.scale,
          overflow: "hidden",
        }
      : {}),
  };
  const widgets = config.widgets.map((id) => (
    <Widget
      key={id}
      id={id}
      frame={frame}
      laps={laps}
      card={card}
      layout={config.layout}
      bare={bare}
      scale={config.widgetScales[id] ?? 1}
    />
  ));

  const badge = placeholder ? <PlaceholderBadge /> : null;

  if (config.layout === "grid") {
    return (
      <div className={config.size ? "" : "min-h-full"} style={frameStyle}>
        <div className="mx-auto grid max-w-md grid-cols-2 gap-2 font-tabular">{widgets}</div>
        {badge}
      </div>
    );
  }

  const justify =
    config.align === "top" ? "items-start" : config.align === "center" ? "items-center" : "items-end";
  if (config.layout === "stack") {
    return (
      <div
        className={`flex justify-start ${justify} ${config.size ? "" : "h-full"}`}
        style={frameStyle}
      >
        <div className="flex w-56 flex-col gap-2 font-tabular">{widgets}</div>
        {badge}
      </div>
    );
  }
  return (
    <div
      className={`flex justify-center ${justify} ${config.size ? "" : "h-full"}`}
      style={frameStyle}
    >
      <div
        className={`flex items-stretch gap-3 rounded-2xl px-4 py-3 font-tabular ${
          bare ? "" : "border border-edge backdrop-blur-sm"
        }`}
        style={card}
      >
        {config.widgets.map((id) => (
          <Widget
            key={id}
            id={id}
            frame={frame}
            laps={laps}
            card={{}}
            layout="strip"
            bare={bare}
            scale={config.widgetScales[id] ?? 1}
          />
        ))}
      </div>
      {badge}
    </div>
  );
}

interface WidgetProps {
  id: WidgetId;
  frame: LiveFrame;
  laps: LapSummary[];
  card: CSSProperties;
  layout: OverlayConfig["layout"];
  bare: boolean;
  scale: number; // per-widget, on top of the global scale
}

function Widget({ id, frame, laps, card, layout, bare, scale }: WidgetProps) {
  const inStrip = layout === "strip";
  const Comp = WIDGET_COMPONENTS[id];
  const body = (
    <Comp
      frame={frame}
      laps={laps}
      variant={WIDGET_META[id].defaultVariant}
      w={1}
      h={1}
      options={{ big: !inStrip }}
    />
  );
  if (inStrip || WIDGET_META[id].frameless) {
    return scale !== 1 ? (
      <div className="flex items-center" style={{ zoom: scale }}>
        {body}
      </div>
    ) : (
      body
    );
  }
  return (
    <div
      className={`rounded-xl p-3 ${bare ? "" : "border border-edge"}`}
      style={{ ...card, ...(scale !== 1 ? { zoom: scale } : {}) }}
    >
      {body}
    </div>
  );
}
