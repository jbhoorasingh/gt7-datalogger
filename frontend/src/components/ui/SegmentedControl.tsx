// Segmented single-choice control built on Radix ToggleGroup — replaces the
// ad-hoc button rows for layout/alignment/page/source pickers with proper
// radio-like semantics and arrow-key navigation.

import * as ToggleGroup from "@radix-ui/react-toggle-group";

export function SegmentedControl<T extends string>({
  value,
  onValueChange,
  options,
  ariaLabel,
  disabled = false,
  size = "md",
}: {
  value: T;
  onValueChange: (value: T) => void;
  /** An option can be disabled, with a title saying why. */
  options: { value: T; label: React.ReactNode; disabled?: boolean; title?: string }[];
  ariaLabel: string;
  disabled?: boolean;
  /** "sm" is the dense variant used inside toolbars and transports. */
  size?: "sm" | "md";
}) {
  return (
    <ToggleGroup.Root
      type="single"
      value={value}
      onValueChange={(v) => {
        // Radix reports a click on the pressed option as a deselect. One
        // option is always active, so it counts as choosing that option
        // again: where the pressed option is only a fallback (the map's
        // Inputs standing in for a lens it cannot draw), that click is how
        // the fallback gets chosen for real.
        onValueChange(v ? (v as T) : value);
      }}
      aria-label={ariaLabel}
      disabled={disabled}
      className="inline-flex shrink-0 overflow-hidden rounded border border-edge"
    >
      {options.map((o) => (
        <ToggleGroup.Item
          key={o.value}
          value={o.value}
          disabled={o.disabled}
          title={o.title}
          className={`${
            size === "sm" ? "px-2.5 py-1 text-[10.5px]" : "px-3 py-1.5 text-xs"
          } font-tabular text-ink-faint transition-colors hover:text-ink disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-ink-faint data-[state=on]:bg-accent/16 data-[state=on]:text-accent-300`}
        >
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}
