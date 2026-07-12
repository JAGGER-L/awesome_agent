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
          value: choice.decision,
          label: choice.label,
          ...(choice.description === undefined
            ? {}
            : { description: choice.description }),
          selected: index === 0,
        })),
      }}
    />
  );
}
