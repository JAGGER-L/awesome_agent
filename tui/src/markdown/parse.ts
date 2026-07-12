import { marked, type Token, type Tokens } from "marked";

import type { MarkdownInline, MarkdownNode } from "./model.js";

export function parseTerminalMarkdown(source: string): readonly MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  for (const part of splitBlockMath(source)) {
    if (part.kind === "math") nodes.push(part);
    else nodes.push(...parseBlocks(marked.lexer(part.text, { gfm: true })));
  }
  return nodes;
}

function parseBlocks(tokens: readonly Token[]): MarkdownNode[] {
  const nodes: MarkdownNode[] = [];
  for (const token of tokens) {
    switch (token.type) {
      case "space":
      case "def":
        break;
      case "heading": {
        const heading = token as Tokens.Heading;
        nodes.push({
          kind: "heading",
          depth: heading.depth,
          children: parseInline(heading.tokens),
        });
        break;
      }
      case "paragraph": {
        const paragraph = token as Tokens.Paragraph;
        nodes.push({
          kind: "paragraph",
          children: parseInline(paragraph.tokens),
        });
        break;
      }
      case "text": {
        const text = token as Tokens.Text;
        nodes.push({
          kind: "paragraph",
          children: text.tokens
            ? parseInline(text.tokens)
            : [{ kind: "text", text: text.text }],
        });
        break;
      }
      case "list": {
        const list = token as Tokens.List;
        nodes.push({
          kind: "list",
          ordered: list.ordered,
          start: typeof list.start === "number" ? list.start : 1,
          items: list.items.map((item) => inlineFromListItem(item)),
        });
        break;
      }
      case "blockquote": {
        const quote = token as Tokens.Blockquote;
        nodes.push({ kind: "quote", children: parseBlocks(quote.tokens) });
        break;
      }
      case "code": {
        const code = token as Tokens.Code;
        nodes.push({
          kind: "code",
          text: code.text,
          ...(code.lang ? { language: code.lang } : {}),
        });
        break;
      }
      case "table": {
        const table = token as Tokens.Table;
        nodes.push({
          kind: "table",
          header: table.header.map((cell) => parseInline(cell.tokens)),
          rows: table.rows.map((row) =>
            row.map((cell) => parseInline(cell.tokens)),
          ),
          align: table.align,
        });
        break;
      }
      case "hr":
        nodes.push({ kind: "rule" });
        break;
      case "html": {
        const html = token as Tokens.HTML;
        nodes.push({
          kind: "paragraph",
          children: [{ kind: "text", text: html.raw }],
        });
        break;
      }
      default:
        nodes.push({
          kind: "paragraph",
          children: [{ kind: "text", text: token.raw }],
        });
    }
  }
  return nodes;
}

function inlineFromListItem(item: Tokens.ListItem): readonly MarkdownInline[] {
  const inline: MarkdownInline[] = [];
  for (const token of item.tokens) {
    if (token.type === "text" || token.type === "paragraph") {
      const candidate = token as Tokens.Text | Tokens.Paragraph;
      inline.push(
        ...(candidate.tokens
          ? parseInline(candidate.tokens)
          : [{ kind: "text" as const, text: candidate.text }]),
      );
    } else {
      inline.push({ kind: "text", text: token.raw.trim() });
    }
  }
  return inline;
}

function parseInline(tokens: readonly Token[]): MarkdownInline[] {
  return tokens.flatMap((token): readonly MarkdownInline[] => {
    switch (token.type) {
      case "text":
      case "escape":
      case "html":
        return token.type === "html"
          ? [{ kind: "text", text: token.raw }]
          : splitInlineMath((token as Tokens.Text | Tokens.Escape).text);
      case "strong":
        return [
          {
            kind: "strong",
            children: parseInline((token as Tokens.Strong).tokens),
          },
        ];
      case "em":
        return [
          {
            kind: "emphasis",
            children: parseInline((token as Tokens.Em).tokens),
          },
        ];
      case "del":
        return [
          {
            kind: "deleted",
            children: parseInline((token as Tokens.Del).tokens),
          },
        ];
      case "codespan":
        return [{ kind: "inline_code", text: (token as Tokens.Codespan).text }];
      case "link": {
        const link = token as Tokens.Link;
        return [
          {
            kind: "link",
            href: link.href,
            children: parseInline(link.tokens),
          },
        ];
      }
      case "image": {
        const image = token as Tokens.Image;
        return [
          {
            kind: "link",
            href: image.href,
            children: [{ kind: "text", text: image.text }],
          },
        ];
      }
      case "br":
        return [{ kind: "break" }];
      default:
        return [{ kind: "text", text: token.raw }];
    }
  });
}

function splitInlineMath(text: string): readonly MarkdownInline[] {
  const result: MarkdownInline[] = [];
  let cursor = 0;
  for (const match of text.matchAll(/\$([^$\n]+)\$/gu)) {
    const index = match.index;
    if (index > cursor)
      result.push({ kind: "text", text: text.slice(cursor, index) });
    result.push({ kind: "math", text: match[1] ?? "" });
    cursor = index + match[0].length;
  }
  if (cursor < text.length)
    result.push({ kind: "text", text: text.slice(cursor) });
  return result.length > 0 ? result : [{ kind: "text", text }];
}

function splitBlockMath(
  source: string,
): readonly (
  | { kind: "text"; text: string }
  | { kind: "math"; text: string }
)[] {
  const parts: (
    | { kind: "text"; text: string }
    | { kind: "math"; text: string }
  )[] = [];
  let cursor = 0;
  const expression = /^\s*\$\$\s*\r?\n([\s\S]*?)\r?\n\s*\$\$\s*$/gmu;
  for (const match of source.matchAll(expression)) {
    if (match.index > cursor)
      parts.push({ kind: "text", text: source.slice(cursor, match.index) });
    parts.push({ kind: "math", text: match[1] ?? "" });
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length)
    parts.push({ kind: "text", text: source.slice(cursor) });
  return parts.length > 0 ? parts : [{ kind: "text", text: source }];
}
