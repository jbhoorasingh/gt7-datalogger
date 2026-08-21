// Widget id → React component. Metadata (variants, sizes, labels) lives in
// widgetMeta.ts so data-only code can import it without React.

import type { ComponentType } from "react";
import { AidsWidget } from "@/components/widgets/AidsWidget";
import { AlertsWidget } from "@/components/widgets/AlertsWidget";
import { BoostWidget } from "@/components/widgets/BoostWidget";
import { ClockWidget } from "@/components/widgets/ClockWidget";
import { DeltaWidget } from "@/components/widgets/DeltaWidget";
import { EngineWidget } from "@/components/widgets/EngineWidget";
import { FuelWidget } from "@/components/widgets/FuelWidget";
import { GearWidget } from "@/components/widgets/GearWidget";
import { InputsWidget } from "@/components/widgets/InputsWidget";
import { PositionWidget } from "@/components/widgets/PositionWidget";
import { RpmWidget } from "@/components/widgets/RpmWidget";
import { SpeedWidget } from "@/components/widgets/SpeedWidget";
import { SteeringWidget } from "@/components/widgets/SteeringWidget";
import { StrategyWidget } from "@/components/widgets/StrategyWidget";
import { TimesWidget } from "@/components/widgets/TimesWidget";
import { TiresWidget } from "@/components/widgets/TiresWidget";
import type { WidgetId } from "./overlay";
import type { WidgetRenderProps } from "./widgetMeta";

export const WIDGET_COMPONENTS: Record<WidgetId, ComponentType<WidgetRenderProps>> = {
  gear: GearWidget,
  speed: SpeedWidget,
  rpm: RpmWidget,
  inputs: InputsWidget,
  steering: SteeringWidget,
  times: TimesWidget,
  delta: DeltaWidget,
  position: PositionWidget,
  tires: TiresWidget,
  fuel: FuelWidget,
  strategy: StrategyWidget,
  clock: ClockWidget,
  engine: EngineWidget,
  aids: AidsWidget,
  boost: BoostWidget,
  alerts: AlertsWidget,
};
