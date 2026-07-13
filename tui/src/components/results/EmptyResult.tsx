import { Text } from "ink";

import { ResultPanel } from "./ResultPanel.js";

export function EmptyResult({
  title,
  message,
  width,
}: {
  readonly title: string;
  readonly message: string;
  readonly width: number;
}) {
  return (
    <ResultPanel title={title} tone="info" width={width}>
      <Text>○ {message}</Text>
    </ResultPanel>
  );
}
