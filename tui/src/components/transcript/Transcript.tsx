import { Static } from "ink";

import { Welcome, type WelcomeProps } from "../Welcome.js";
import type { TranscriptBlock } from "../../transcript/model.js";
import { BlockView } from "./blocks/BlockView.js";

type StaticItem =
  | {
      readonly kind: "welcome";
      readonly key: "welcome";
      readonly props: WelcomeProps;
    }
  | {
      readonly kind: "block";
      readonly key: string;
      readonly block: TranscriptBlock;
    };

export function Transcript({
  blocks,
  width,
  welcome,
  toolDetailsExpanded = false,
}: {
  blocks: readonly TranscriptBlock[];
  width: number;
  welcome?: Omit<WelcomeProps, "width"> | undefined;
  toolDetailsExpanded?: boolean;
}) {
  const items: StaticItem[] = [
    ...(welcome
      ? [
          {
            kind: "welcome" as const,
            key: "welcome" as const,
            props: { ...welcome, width },
          },
        ]
      : []),
    ...blocks.map((block) => ({
      kind: "block" as const,
      key: block.key,
      block,
    })),
  ];
  return (
    <Static items={items}>
      {(item) =>
        item.kind === "welcome" ? (
          <Welcome key={item.key} {...item.props} />
        ) : (
          <BlockView
            key={item.key}
            block={item.block}
            width={width}
            toolDetailsExpanded={toolDetailsExpanded}
          />
        )
      }
    </Static>
  );
}
