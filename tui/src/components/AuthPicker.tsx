import type { PickerSelection } from "../interaction/model.js";
import { SelectionPanel } from "./interactions/index.js";

export function AuthPicker({
  selection,
  selected,
  width,
}: {
  readonly selection: NonNullable<PickerSelection>;
  readonly selected: number;
  readonly width?: number;
}) {
  const services = selection.options.some((option) => option.value === "mem0");
  return (
    <SelectionPanel
      title={selection.prompt}
      options={selection.options}
      selected={selected}
      variant="neutral"
      width={width}
      sectionForOption={
        services
          ? (value) =>
              value === "deepseek"
                ? "Model providers"
                : value === "mem0"
                  ? "Memory providers"
                  : undefined
          : undefined
      }
    />
  );
}
