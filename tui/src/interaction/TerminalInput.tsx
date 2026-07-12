import { useInput } from "ink";

import { normalizeTerminalKey, type TerminalKey } from "./key-router.js";

export function TerminalInput({
  active = true,
  onInput,
}: {
  readonly active?: boolean;
  readonly onInput: (input: string, key: TerminalKey) => void;
}) {
  useInput(
    (input, key) => {
      onInput(input, normalizeTerminalKey(key));
    },
    { isActive: active },
  );
  return null;
}
