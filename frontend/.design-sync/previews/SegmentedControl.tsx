import { SegmentedControl } from "gt7-datalogger-frontend";
import { useState } from "react";
import { Labelled, Panel, Row, Surface } from "../preview-shell";

// The app's four real segmented pickers: the layout kind and the page behind
// the widgets (LayoutBuilder), the telemetry source and packet format (Admin),
// the playback transport's speed and the guide's section (both dense "sm").

// The control is controlled, so each cell keeps the choice it is showing.
function Segmented<T extends string>({
  initial,
  options,
  ariaLabel,
  size = "md",
  disabled = false,
}: {
  initial: T;
  options: { value: T; label: React.ReactNode }[];
  ariaLabel: string;
  size?: "sm" | "md";
  disabled?: boolean;
}) {
  const [value, setValue] = useState<T>(initial);
  return (
    <SegmentedControl
      ariaLabel={ariaLabel}
      value={value}
      onValueChange={setValue}
      options={options}
      size={size}
      disabled={disabled}
    />
  );
}

/** Admin's telemetry source picker, with the field label the settings form
 *  puts above it. */
export function TelemetrySource() {
  return (
    <Surface>
      <span className="mb-1 block text-xs text-ink-dim">Telemetry source</span>
      <Segmented
        ariaLabel="Telemetry source"
        initial="sim"
        options={[
          { value: "udp", label: "PlayStation" },
          { value: "sim", label: "Simulated" },
        ]}
      />
    </Surface>
  );
}

/** Three options and the longest labels the control carries — the OBS
 *  layout's page treatment. */
export function PageBehindWidgets() {
  return (
    <Surface>
      <span className="mb-1 block text-xs text-ink-dim">Page behind the widgets</span>
      <Segmented
        ariaLabel="Page behind the widgets"
        initial="transparent"
        options={[
          { value: "transparent", label: "Transparent" },
          { value: "green", label: "Green screen" },
          { value: "dark", label: "Solid dark" },
        ]}
      />
    </Surface>
  );
}

/** The dense variant, as the transports use it: playback speed, the race-line
 *  map's sync mode, and the guide's section tabs with their counts. */
export function DenseToolbars() {
  return (
    <Surface>
      <Row className="gap-4">
        <Labelled label="playback speed">
          <Segmented
            ariaLabel="Playback speed"
            size="sm"
            initial="1"
            options={[0.25, 0.5, 1, 2, 4].map((s) => ({ value: String(s), label: `${s}×` }))}
          />
        </Labelled>
        <Labelled label="race line sync">
          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-ink-faint">
            sync
            <Segmented
              ariaLabel="Race line sync"
              size="sm"
              initial="time"
              options={[
                { value: "time", label: "Time" },
                { value: "position", label: "Position" },
              ]}
            />
          </span>
        </Labelled>
        <Labelled label="guide section">
          <Segmented
            ariaLabel="Guide section"
            size="sm"
            initial="features"
            options={[
              { value: "features", label: "Features · 14" },
              { value: "channels", label: "Channels · 26" },
            ]}
          />
        </Labelled>
      </Row>
    </Surface>
  );
}

/** md against sm at the same option set, and the disabled state the settings
 *  form puts the pickers in while an apply is in flight. */
export function SizesAndDisabled() {
  const kinds = [
    { value: "overlay", label: "OBS overlay" },
    { value: "dash", label: "Driver dash" },
  ];
  return (
    <Surface>
      <Panel title="Layout kind">
        <Row className="gap-4">
          <Labelled label="md">
            <Segmented ariaLabel="Layout kind" initial="overlay" options={kinds} />
          </Labelled>
          <Labelled label="sm">
            <Segmented ariaLabel="Layout kind, dense" size="sm" initial="overlay" options={kinds} />
          </Labelled>
          <Labelled label="disabled — applying">
            <Segmented
              ariaLabel="Telemetry source"
              initial="sim"
              disabled
              options={[
                { value: "udp", label: "PlayStation" },
                { value: "sim", label: "Simulated" },
              ]}
            />
          </Labelled>
        </Row>
      </Panel>
    </Surface>
  );
}

/** Four short options: the GT7 packet format, with the hint the form prints
 *  under it. */
export function PacketFormat() {
  return (
    <Surface width={280}>
      <span className="mb-1 block text-xs text-ink-dim">Packet format</span>
      <Segmented
        ariaLabel="Packet format"
        initial="C"
        options={[
          { value: "A", label: "A" },
          { value: "B", label: "B" },
          { value: "~", label: "~" },
          { value: "C", label: "C" },
        ]}
      />
      <p className="mt-1 text-[11px] text-ink-dim">
        C is richest (needs GT7 v1.68+); use A for older game versions.
      </p>
    </Surface>
  );
}
