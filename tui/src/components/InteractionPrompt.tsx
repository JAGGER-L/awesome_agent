import type { PendingInteraction } from "../interaction/model.js";
import { Picker } from "./Picker.js";

export function InteractionPrompt({
  interaction,
  selected,
  submitting = false,
  message,
}: {
  readonly interaction: PendingInteraction;
  readonly selected: number;
  readonly submitting?: boolean;
  readonly message?: string;
}) {
  return (
    <Picker
      selected={selected}
      selection={{
        prompt: message ?? (submitting ? "Submitting…" : interaction.prompt),
        options: interaction.choices.map((choice, index) => ({
          value: choice,
          label: choice,
          selected: index === 0,
        })),
      }}
    />
  );
}
