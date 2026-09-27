import { SETTING_LABELS, type EditableKey } from "./model";

// Fixed bottom-centre bar while buffered edits differ from the server.
export function PendingBar({
  keys,
  applying,
  onDiscard,
  onApply,
}: {
  keys: EditableKey[];
  applying: boolean;
  onDiscard: () => void;
  onApply: () => void;
}) {
  if (keys.length === 0) return null;
  return (
    <div className="fixed bottom-[18px] left-1/2 z-10 w-[min(640px,calc(100%-40px))] -translate-x-1/2">
      <div
        role="region"
        aria-label="Unsaved settings"
        className="elevated flex items-center gap-3 rounded-lg bg-panel px-3.5 py-2.5"
      >
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warn" />
        <div className="min-w-0 flex-1">
          <div className="text-[12.5px]">
            {keys.length === 1 ? "1 change" : `${keys.length} changes`}
          </div>
          <div className="truncate text-[11px] text-ink-faint">
            {keys.map((k) => SETTING_LABELS[k]).join(" · ")}
          </div>
        </div>
        <button className="btn" disabled={applying} onClick={onDiscard}>
          Discard
        </button>
        <button className="btn btn-primary" disabled={applying} onClick={onApply}>
          {applying ? "Applying…" : "Apply changes"}
        </button>
      </div>
    </div>
  );
}
