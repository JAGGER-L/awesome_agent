import { Text } from "ink";

import type { CommandPresentation } from "../commands/presenters.js";
import { MarkdownBlock } from "../markdown/MarkdownBlock.js";
import {
  AlignedRows,
  EmptyResult,
  ResultNotice,
  ResultPanel,
} from "./results/index.js";

export function CommandResultView({
  presentation,
  width,
}: {
  readonly presentation: CommandPresentation;
  readonly width: number;
}) {
  switch (presentation.kind) {
    case "panel":
      return (
        <ResultPanel
          title={presentation.title}
          tone={presentation.tone}
          width={width}
        >
          <AlignedRows rows={presentation.rows} width={width} />
        </ResultPanel>
      );
    case "notice":
    case "progress":
      return (
        <ResultNotice message={presentation.message} tone={presentation.tone} />
      );
    case "empty":
      return (
        <EmptyResult
          title={presentation.title}
          message={presentation.message}
          width={width}
        />
      );
    case "markdown":
      return (
        <ResultPanel
          title={presentation.title}
          tone={presentation.tone}
          width={width}
        >
          <MarkdownBlock
            source={presentation.source}
            width={Math.max(1, width - 4)}
          />
        </ResultPanel>
      );
    case "error":
      return (
        <ResultPanel title={presentation.title} tone="danger" width={width}>
          <Text>{presentation.message}</Text>
        </ResultPanel>
      );
    default:
      return assertNever(presentation);
  }
}

function assertNever(value: never): never {
  throw new Error(`Unhandled command presentation: ${String(value)}`);
}
