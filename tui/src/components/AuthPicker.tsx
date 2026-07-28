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
  const serviceCatalog = selection.options.some(
    (option) => option.value === "mem0" || option.value === "tavily",
  );
  return (
    <SelectionPanel
      title={selection.prompt}
      options={selection.options}
      selected={selected}
      variant="neutral"
      width={width}
      sectionForOption={
        serviceCatalog
          ? (value) =>
              value === "deepseek"
                ? "Model providers"
                : value === "mem0"
                  ? "Memory providers"
                  : value === "tavily"
                    ? "Web providers"
                    : undefined
          : undefined
      }
    />
  );
}
