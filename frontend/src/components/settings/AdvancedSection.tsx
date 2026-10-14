// Settings › Advanced: every setting the server has, grouped, each with its
// environment variable, default, where its value comes from, and when a
// change takes effect. The other sections stay the friendly way to change
// the everyday ones; this is the complete list (and the one the docs'
// configuration table is generated from, server side).

import { useCallback, useEffect, useMemo, useState } from "react";
import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { ConfigDoc, ConfigSetting, ConfigValue } from "@/lib/types";
import { toast } from "@/store/toasts";
import {
  NO_EDITS,
  parseNumber,
  pendingCount,
  plainDoc,
  resetKey,
  restartPending,
  setValue,
  shownValue,
  type ConfigEdits,
} from "./advanced";
import { INPUT_CLS, SectionPanel, SettingRow } from "./parts";

const SOURCE_LABEL: Record<ConfigSetting["source"], string> = {
  saved: "set here",
  env: "from environment",
  default: "default",
};

function formatDefault(row: ConfigSetting): string {
  const d = row.default;
  if (row.secret || d === null) return "";
  if (Array.isArray(d)) return d.length ? d.join(", ") : "(none)";
  if (d === "") return "(empty)";
  return String(d);
}

function Control({
  row,
  value,
  onChange,
}: {
  row: ConfigSetting;
  value: ConfigValue | null;
  onChange: (v: ConfigValue) => void;
}) {
  const disabled = !row.editable;
  const id = `cfg-${row.key}`;
  // Number fields keep their own text, so "4." or "" can be typed on the way
  // to a number without the field snapping back.
  const [text, setText] = useState(value == null ? "" : String(value));
  useEffect(() => setText(value == null ? "" : String(value)), [value]);

  if (row.kind === "bool") {
    return <Toggle ariaLabel={row.label} checked={value === true} disabled={disabled} onCheckedChange={onChange} />;
  }
  if (row.kind === "list" && row.choices) {
    const on = Array.isArray(value) ? value : [];
    return (
      <div className="flex flex-wrap gap-1.5">
        {row.choices.map((c) => {
          const checked = on.includes(c);
          return (
            <button
              key={c}
              type="button"
              aria-pressed={checked}
              disabled={disabled}
              onClick={() => onChange(checked ? on.filter((x) => x !== c) : [...on, c])}
              className={`rounded border px-2 py-0.5 font-tabular text-[11px] transition-colors ${
                checked ? "border-accent/60 bg-accent/16 text-accent-300" : "border-edge text-ink-faint hover:text-ink"
              }`}
            >
              {c}
            </button>
          );
        })}
      </div>
    );
  }
  if (row.choices) {
    return (
      <Select
        ariaLabel={row.label}
        value={typeof value === "string" ? value : ""}
        onValueChange={onChange}
        options={row.choices.map((c) => ({ value: c, label: c }))}
        className="h-8 min-w-[160px] px-3 text-[13px]"
      />
    );
  }
  if (row.kind === "int" || row.kind === "float") {
    return (
      <input
        id={id}
        type="number"
        inputMode={row.kind === "int" ? "numeric" : "decimal"}
        min={row.min}
        max={row.max}
        step={row.kind === "int" ? 1 : "any"}
        disabled={disabled}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const n = parseNumber(row, e.target.value);
          if (n !== null) onChange(n);
        }}
        className={`${INPUT_CLS} max-w-[160px] disabled:opacity-60`}
      />
    );
  }
  return (
    <input
      id={id}
      type={row.secret ? "password" : "text"}
      autoComplete="off"
      spellCheck={false}
      disabled={disabled}
      value={typeof value === "string" ? value : ""}
      placeholder={row.secret ? (row.is_set ? `set (${row.hint || "hidden"}) — type to replace` : "not set") : ""}
      onChange={(e) => onChange(e.target.value)}
      className={`${INPUT_CLS} w-full disabled:opacity-60`}
    />
  );
}

export function AdvancedSection({ onApplied }: { onApplied: () => void }) {
  const [doc, setDoc] = useState<ConfigDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [edits, setEdits] = useState<ConfigEdits>(NO_EDITS);
  const [applying, setApplying] = useState(false);

  const load = useCallback(() => {
    api.admin
      .config()
      .then((d) => {
        setDoc(d);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load settings"));
  }, []);
  useEffect(load, [load]);

  const rows = useMemo(() => doc?.settings ?? [], [doc]);
  const count = pendingCount(edits);
  const waiting = restartPending(rows);

  async function apply() {
    setApplying(true);
    try {
      const next = await api.admin.updateConfig(edits);
      setDoc(next);
      setEdits(NO_EDITS);
      toast("Settings applied", "success");
      onApplied();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Applying settings failed", "error");
    } finally {
      setApplying(false);
    }
  }

  return (
    <SectionPanel
      title="Advanced"
      description="Every setting this server has, with its environment variable and default. A value set here overrides the environment until you reset it."
      actions={
        count > 0 && (
          <>
            <button className="btn" disabled={applying} onClick={() => setEdits(NO_EDITS)}>
              Discard
            </button>
            <button className="btn btn-primary" disabled={applying} onClick={() => void apply()}>
              Apply {count} change{count === 1 ? "" : "s"}
            </button>
          </>
        )
      }
    >
      {error && <div className="px-[18px] py-3 text-[12px] text-brake">{error}</div>}
      {waiting.length > 0 && (
        <div className="px-[18px] py-2.5 text-[12px] text-warn">
          Restart the server to use the new {waiting.map((r) => r.label).join(", ")}.
        </div>
      )}
      {doc?.groups.map((group) => {
        const inGroup = rows.filter((r) => r.group === group);
        return (
          <div key={group}>
            <div className="section-header px-[18px] pb-1 pt-3">{group}</div>
            {inGroup.map((row, i) => {
              const resetting = edits.reset.includes(row.key);
              const changed = row.key in edits.values || resetting;
              const def = formatDefault(row);
              return (
                <SettingRow
                  key={row.key}
                  label={row.label}
                  htmlFor={`cfg-${row.key}`}
                  last={i === inGroup.length - 1}
                  help={
                    <>
                      <span className="font-mono text-[10.5px] text-ink-faint">{row.env}</span>
                      <span className="mt-0.5 block">{plainDoc(row.doc)}</span>
                    </>
                  }
                >
                  <div className="flex flex-col gap-1.5">
                    <Control
                      row={row}
                      value={shownValue(row, edits)}
                      onChange={(v) => setEdits((e) => setValue(e, row, v))}
                    />
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-tabular text-[10.5px] text-ink-faint">
                      <span className={changed ? "text-accent" : ""}>
                        {resetting ? "will reset" : changed ? "changed" : SOURCE_LABEL[row.source]}
                      </span>
                      {def && (row.source !== "default" || changed) && <span>default {def}</span>}
                      {row.apply === "restart" && <span>applies at next start</span>}
                      {row.apply === "env" && <span>environment only</span>}
                      {row.restart_pending && <span className="text-warn">restart pending</span>}
                      {row.editable && row.source === "saved" && !resetting && (
                        <button
                          type="button"
                          className="text-ink-dim underline-offset-2 hover:text-ink hover:underline"
                          title="Forget the value set here: the environment's value, or the default"
                          onClick={() => setEdits((e) => resetKey(e, row.key))}
                        >
                          Reset
                        </button>
                      )}
                    </div>
                  </div>
                </SettingRow>
              );
            })}
          </div>
        );
      })}
    </SectionPanel>
  );
}
