import type { PickerSelection } from "../interaction/model.js";
import { SelectionPanel, type SelectionVariant } from "./interactions/index.js";

export function Picker({
  selection,
  selected,
  variant = "neutral",
}: {
  readonly selection: PickerSelection;
  readonly selected: number;
  readonly variant?: SelectionVariant;
}) {
  return (
    <SelectionPanel
      title={selection.prompt}
      options={selection.options}
      selected={selected}
      variant={variant}
    />
  );
}
