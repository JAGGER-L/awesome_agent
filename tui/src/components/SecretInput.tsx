import { Box, Text, useInput } from "ink";
import { useRef, useState } from "react";

import { graphemes } from "../composer/graphemes.js";
import { useTheme } from "./theme.js";

export interface SecretInputProps {
  readonly label: string;
  readonly active?: boolean;
  readonly submitting?: boolean;
  readonly message?: string;
  readonly onSubmit: (value: string) => void;
  readonly onCancel: () => void;
}

export function SecretInput({
  label,
  active = true,
  submitting = false,
  message,
  onSubmit,
  onCancel,
}: SecretInputProps) {
  const [length, setLength] = useState(0);
  const value = useRef("");
  const theme = useTheme();

  useInput(
    (input, key) => {
      if (submitting) return;
      if (key.escape) {
        value.current = "";
        setLength(0);
        onCancel();
        return;
      }
      if (key.return) {
        if (!value.current) return;
        const submitted = value.current;
        value.current = "";
        setLength(0);
        onSubmit(submitted);
        return;
      }
      if (key.backspace || key.delete) {
        const parts = graphemes(value.current);
        parts.pop();
        value.current = parts.join("");
        setLength(parts.length);
        return;
      }
      if (key.ctrl || key.meta || !input) return;
      value.current += input;
      setLength(graphemes(value.current).length);
    },
    { isActive: active },
  );

  return (
    <Box flexDirection="column">
      <Text color={theme.accent}>{label}</Text>
      <Text>{"•".repeat(length)}</Text>
      <Text dimColor>Enter to save · Esc to cancel</Text>
      {message ? <Text color={theme.warning}>{message}</Text> : null}
    </Box>
  );
}
