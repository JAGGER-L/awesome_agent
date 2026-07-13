import { Text } from "ink";

import type { CommandPresentation } from "../commands/presenters.js";
import { MarkdownBlock } from "../markdown/MarkdownBlock.js";
import {
  AlignedRows,
  EmptyResult,
  ExpandableDetails,
  ResultNotice,
  ResultPanel,
} from "./results/index.js";

export function CommandResultView({
  presentation,
  width,
  detailsExpanded = false,
}: {
  readonly presentation: CommandPresentation;
  readonly width: number;
  readonly detailsExpanded?: boolean;
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
    case "diff":
      return (
        <ResultPanel title={presentation.title} tone="info" width={width}>
          {presentation.changeSetId ? (
            <Text>Change set · {presentation.changeSetId}</Text>
          ) : null}
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
    case "change": {
      const count = presentation.paths.length;
      return (
        <ExpandableDetails
          expanded={detailsExpanded}
          summary={
            <Text>
              ✓ {presentation.title} · {count} {count === 1 ? "file" : "files"}{" "}
              · {presentation.lifecycle}
            </Text>
          }
        >
          <ResultPanel
            title={presentation.title}
            tone={presentation.warning ? "warning" : "success"}
            width={width}
          >
            <AlignedRows
              width={width}
              rows={[
                { label: "Change set", value: presentation.changeSetId },
                { label: "Lifecycle", value: presentation.lifecycle },
                ...presentation.paths.map((path, index) => ({
                  label: `File ${index + 1}`,
                  value: path,
                })),
                ...(presentation.warning
                  ? [
                      {
                        label: "Warning",
                        value: presentation.warning,
                        status: "warning" as const,
                      },
                    ]
                  : []),
              ]}
            />
          </ResultPanel>
        </ExpandableDetails>
      );
    }
    default:
      return assertNever(presentation);
  }
}

function assertNever(value: never): never {
  throw new Error(`Unhandled command presentation: ${String(value)}`);
}
