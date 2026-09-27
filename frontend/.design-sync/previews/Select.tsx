import { EXCLUDE_REASONS, formatLapTime, Select } from "gt7-datalogger-frontend";
import { useState } from "react";
import { LAPS, SESSIONS } from "../fixtures/app";
import { Labelled, Panel, Row, Surface } from "../preview-shell";

// The app's real pickers: the Analysis toolbar's session and reference-lap
// selects, Admin's log level, the layout builder's widget size, and the lap
// table's "why is this lap excluded" picker, which is the placeholder case.
//
// Only the trigger is captured: the listbox is a Radix portal opened by
// pointer or keyboard, and this wrapper forwards no `open`/`defaultOpen`.

function Picker({
  initial,
  ...rest
}: { initial: string } & Omit<Parameters<typeof Select>[0], "value" | "onValueChange">) {
  const [value, setValue] = useState(initial);
  return <Select value={value} onValueChange={setValue} {...rest} />;
}

/** The Analysis toolbar's session picker: the longest option set in the app,
 *  and the reason the trigger truncates instead of growing. */
export function SessionPicker() {
  return (
    <Surface width={300}>
      <Picker
        ariaLabel="Session"
        initial={String(SESSIONS[2]!.id)}
        options={SESSIONS.map((s) => ({
          value: String(s.id),
          label: `#${s.id}${s.track_name ? ` · ${s.track_name}` : ""} · ${s.car_name} · ${
            s.lap_count
          } laps`,
        }))}
        className="max-w-full px-2.5 py-[5px] text-xs"
      />
    </Surface>
  );
}

/** The `bare` variant — the toolbar's reference-lap picker, where the control
 *  is the text, sat next to its inline label. */
export function BareReferenceLap() {
  return (
    <Surface>
      <div className="flex items-center gap-1.5 text-[11.5px] text-ink-dim">
        <span>ref</span>
        <Picker
          ariaLabel="Reference lap"
          initial={String(LAPS[6]!.id)}
          options={LAPS.map((lap) => ({
            value: String(lap.id),
            label: `L${lap.number} · ${formatLapTime(lap.time_ms)}`,
          }))}
          variant="bare"
          className="px-1 font-tabular text-[11.5px]"
        />
      </div>
    </Surface>
  );
}

/** Nothing chosen yet: the lap table asks why a lap was excluded, and the
 *  trigger carries the placeholder until it is answered. */
export function Placeholder() {
  return (
    <Surface>
      <Panel title="Lap 5 · excluded by hand">
        <Row className="gap-4">
          <Labelled label="unanswered">
            <Picker
              ariaLabel="Why lap 5 is excluded"
              initial=""
              placeholder="why?"
              options={EXCLUDE_REASONS.map((r) => ({ value: r, label: r }))}
              className="min-w-0 px-1.5 py-px font-sans text-[10.5px]"
            />
          </Labelled>
          <Labelled label="answered">
            <Picker
              ariaLabel="Why lap 6 is excluded"
              initial="off-track"
              placeholder="why?"
              options={EXCLUDE_REASONS.map((r) => ({ value: r, label: r }))}
              className="min-w-0 px-1.5 py-px font-sans text-[10.5px]"
            />
          </Labelled>
        </Row>
      </Panel>
    </Surface>
  );
}

/** Two of the settings forms' selects at the sizes they are given: Admin's log
 *  level, and the layout builder's widget size in its label row. */
export function SettingsFields() {
  return (
    <Surface width={280}>
      <Panel>
        <div className="flex flex-col gap-2.5">
          <div>
            <span className="mb-1 block text-xs text-ink-dim">Log level</span>
            <Picker
              ariaLabel="Log level"
              initial="INFO"
              options={["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => ({
                value: l,
                label: l,
              }))}
              className="px-2 py-1.5 text-xs"
            />
          </div>
          <label className="flex items-center justify-between gap-2 text-xs text-ink-dim">
            Size
            <Picker
              ariaLabel="Widget size"
              initial="3x2"
              options={[
                [2, 1],
                [3, 2],
                [4, 2],
                [6, 3],
              ].map(([w, h]) => ({ value: `${w}x${h}`, label: `${w} × ${h}` }))}
              className="px-2 py-1 text-xs"
            />
          </label>
          <label className="flex items-center justify-between gap-2 text-xs text-ink-dim">
            Fine scale
            <Picker
              ariaLabel="Widget scale"
              initial="1"
              options={[0.75, 0.9, 1, 1.25, 1.5].map((s) => ({
                value: String(s),
                label: `${Math.round(s * 100)} %`,
              }))}
              className="px-2 py-1 text-xs"
            />
          </label>
        </div>
      </Panel>
    </Surface>
  );
}
