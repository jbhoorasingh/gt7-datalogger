import {
  AddWidgetPanel,
  BuilderDialogs,
  BuilderToolbar,
  CanvasOptionsPanel,
  CanvasPanel,
  LayoutsPanel,
  LegacyPresetsBanner,
  PresetsPanel,
  SelectedWidgetPanel,
  UsePanel,
  useLayoutBuilder,
} from "@/components/LayoutBuilder";
import { parseHash } from "@/lib/router";

// Overlays and driver dashboards: saved layouts and presets on the left, the
// canvas and its URL in the middle, the selected widget and the palette on
// the right. #/overlays?layout=<id> opens a saved layout (Settings links here).
export function OverlaysView() {
  // App re-renders on every hashchange, so reading the hash here is current.
  const requested = parseHash(window.location.hash).params.get("layout");
  const b = useLayoutBuilder(requested);

  return (
    <div className="grid grid-cols-1 items-start gap-3.5 xl:grid-cols-[220px_minmax(0,1fr)_272px]">
      <aside className="flex flex-col gap-3 xl:sticky xl:top-3">
        <LayoutsPanel b={b} />
        <PresetsPanel b={b} />
      </aside>

      <section className="flex min-w-0 flex-col gap-2.5">
        <LegacyPresetsBanner b={b} />
        <BuilderToolbar b={b} />
        <CanvasPanel b={b} />
        <UsePanel b={b} />
        <CanvasOptionsPanel b={b} />
      </section>

      <aside className="flex flex-col gap-3 xl:sticky xl:top-3">
        <SelectedWidgetPanel b={b} />
        <AddWidgetPanel b={b} />
      </aside>

      <BuilderDialogs b={b} />
    </div>
  );
}
