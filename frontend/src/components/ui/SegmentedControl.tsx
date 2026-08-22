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
  options: { value: T; label: React.ReactNode }[];
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
        if (v) onValueChange(v as T); // ignore deselect — one option is always active
      }}
      aria-label={ariaLabel}
      disabled={disabled}
      className="inline-flex shrink-0 overflow-hidden rounded border border-edge"
    >
      {options.map((o) => (
        <ToggleGroup.Item
          key={o.value}
          value={o.value}
          className={`${
            size === "sm" ? "px-2.5 py-1 text-[10.5px]" : "px-3 py-1.5 text-xs"
          } font-tabular text-ink-faint transition-colors hover:text-ink data-[state=on]:bg-accent/16 data-[state=on]:text-accent-300`}
        >
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}
