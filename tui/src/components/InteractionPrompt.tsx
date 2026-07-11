import { useRef } from "react";

import type { SurfaceState } from "../state/model.js";
import { Picker } from "./Picker.js";

type PendingInteraction = NonNullable<SurfaceState["pending_interaction"]>;

export function InteractionPrompt({
  interaction,
  onRespond,
}: {
  readonly interaction: PendingInteraction;
  readonly onRespond: (decision: string) => void;
}) {
  const responded = useRef(false);
  const respond = (decision: string) => {
    if (responded.current) return;
    responded.current = true;
    onRespond(decision);
  };
  return (
    <Picker
      selection={{
        prompt: interaction.prompt,
        options: interaction.choices.map((choice, index) => ({
          value: choice,
          label: choice,
          selected: index === 0,
        })),
      }}
      onSelect={respond}
      onClose={() => {
        if (interaction.choices.includes("deny")) respond("deny");
      }}
    />
  );
}
