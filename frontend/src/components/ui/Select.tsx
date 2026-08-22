// Themed select built on Radix (shadcn-style owned wrapper): keyboard
// navigation, type-ahead, proper listbox semantics — replaces native
// <select>, which can't be themed to match the dark panel UI.

import * as SelectPrimitive from "@radix-ui/react-select";

export interface SelectOption {
  value: string;
  label: React.ReactNode;
}

// Trigger chrome per variant. Each variant supplies its OWN border,
// background and text colour so `className` never has to fight them:
// competing utilities from the same group resolve by stylesheet order, not
// by which one the caller wrote last.
const TRIGGER_VARIANTS = {
  default:
    "rounded-md border border-edge bg-transparent text-ink-soft hover:border-accent",
  // Inline label-and-value, for a toolbar where the control IS the text.
  bare: "border-0 bg-transparent text-accent hover:text-accent-300",
} as const;

export function Select({
  value,
  onValueChange,
  options,
  className = "",
  ariaLabel,
  variant = "default",
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  className?: string; // sizing/typography for the trigger
  ariaLabel?: string;
  variant?: keyof typeof TRIGGER_VARIANTS;
}) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange}>
      <SelectPrimitive.Trigger
        aria-label={ariaLabel}
        className={`inline-flex items-center justify-between gap-2 transition-colors data-[placeholder]:text-ink-dim ${TRIGGER_VARIANTS[variant]} ${className}`}
      >
        <span className="truncate">
          <SelectPrimitive.Value />
        </span>
        <SelectPrimitive.Icon className="text-[10px] text-ink-faint">▾</SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className="elevated z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-y-auto rounded-panel bg-panel py-1"
        >
          <SelectPrimitive.Viewport>
            {options.map((o) => (
              <SelectPrimitive.Item
                key={o.value}
                value={o.value}
                className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-xs text-ink-soft outline-none data-[highlighted]:bg-panel-2 data-[highlighted]:text-ink data-[state=checked]:text-accent"
              >
                <SelectPrimitive.ItemText>{o.label}</SelectPrimitive.ItemText>
                <SelectPrimitive.ItemIndicator className="ml-auto text-accent">
                  ✓
                </SelectPrimitive.ItemIndicator>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
