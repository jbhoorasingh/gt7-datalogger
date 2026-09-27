// On/off switch that replaces bare checkboxes in settings-style rows: a
// 30×16 pill with a 12px knob that slides 2px → 16px.

export function Toggle({
  checked,
  onCheckedChange,
  ariaLabel,
  disabled = false,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  ariaLabel: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={`relative inline-block h-4 w-[30px] shrink-0 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
        checked ? "bg-accent-700" : "bg-edge"
      }`}
    >
      <span
        className={`absolute top-0.5 h-3 w-3 rounded-full transition-[left] duration-150 ${
          checked ? "left-4 bg-accent-200" : "left-0.5 bg-ink-faint"
        }`}
      />
    </button>
  );
}
