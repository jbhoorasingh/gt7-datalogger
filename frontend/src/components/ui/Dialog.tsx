// In-app modal dialogs replacing native prompt()/confirm(), built on Radix
// Dialog: portal, backdrop, focus trap + restore, Escape, aria wiring.

import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useEffect, useId, useState } from "react";

function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/60" />
        <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-40 w-[calc(100vw-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-edge bg-panel p-4 shadow-xl shadow-black/40">
          <DialogPrimitive.Title className="mb-3 text-sm font-semibold">
            {title}
          </DialogPrimitive.Title>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body?: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog open={open} title={title} onClose={onCancel}>
      {body && (
        <DialogPrimitive.Description className="mb-4 text-sm text-ink-dim">
          {body}
        </DialogPrimitive.Description>
      )}
      <div className="flex justify-end gap-2">
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button className={danger ? "btn-danger" : "btn"} onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </Dialog>
  );
}

export function PromptDialog({
  open,
  title,
  label,
  placeholder,
  submitLabel = "Save",
  initialValue = "",
  suggestions,
  onSubmit,
  onCancel,
}: {
  open: boolean;
  title: string;
  label?: string;
  placeholder?: string;
  submitLabel?: string;
  initialValue?: string;
  // Offered as a datalist rather than a fixed list: the value is free text
  // (a circuit nobody has named yet is a legitimate answer), but where the
  // whole problem is near-miss spellings, the existing names should be one
  // keystroke away.
  suggestions?: string[];
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  // Reset the field each time the dialog opens.
  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  const listId = useId();
  const submit = () => {
    const v = value.trim();
    if (v) onSubmit(v);
  };
  return (
    <Dialog open={open} title={title} onClose={onCancel}>
      {label && (
        <DialogPrimitive.Description className="mb-2 text-xs text-ink-dim">
          {label}
        </DialogPrimitive.Description>
      )}
      <input
        value={value}
        list={suggestions ? listId : undefined}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
        placeholder={placeholder}
        className="mb-4 w-full rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-sm placeholder:text-ink-dim/60 focus:border-accent focus:outline-none"
      />
      {suggestions && (
        <datalist id={listId}>
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
      <div className="flex justify-end gap-2">
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn" disabled={!value.trim()} onClick={submit}>
          {submitLabel}
        </button>
      </div>
    </Dialog>
  );
}
