import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import remarkStringify from "remark-stringify";
import { unified } from "unified";
import { visit } from "unist-util-visit";

const parser = unified().use(remarkParse).use(remarkGfm);
const serializer = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkStringify, {
    bullet: "-",
    fences: true,
    listItemIndent: "one",
  });

function compareCodePoints(left, right) {
  const leftPoints = [...String(left)];
  const rightPoints = [...String(right)];
  const length = Math.min(leftPoints.length, rightPoints.length);
  for (let index = 0; index < length; index += 1) {
    const difference =
      leftPoints[index].codePointAt(0) - rightPoints[index].codePointAt(0);
    if (difference !== 0) return difference;
  }
  return leftPoints.length - rightPoints.length;
}

export function parseMarkdownAst(markdown) {
  return parser.parse(String(markdown));
}

function firstDefinitionsByIdentifier(tree) {
  const definitions = new Map();
  visit(tree, "definition", (node) => {
    if (!definitions.has(node.identifier)) {
      definitions.set(node.identifier, node);
    }
  });
  return definitions;
}

export function markdownLinks(markdown) {
  const links = [];
  const tree = parseMarkdownAst(markdown);
  const definitions = firstDefinitionsByIdentifier(tree);
  visit(tree, (node) => {
    if (node.type === "link") {
      links.push({
        destination: node.url,
        line: node.position?.start.line ?? 1,
        type: "link",
      });
      return;
    }
    if (node.type !== "linkReference") return;

    const definition = definitions.get(node.identifier);
    if (!definition) return;
    links.push({
      destination: definition.url,
      line: definition.position?.start.line ?? node.position?.start.line ?? 1,
      referenceLine: node.position?.start.line ?? 1,
      type: "definition",
    });
  });
  return links.sort(
    (left, right) =>
      left.line - right.line ||
      compareCodePoints(left.type, right.type) ||
      compareCodePoints(left.destination, right.destination) ||
      (left.referenceLine ?? left.line) - (right.referenceLine ?? right.line),
  );
}

export function rewriteMarkdownLinks(markdown, resolveDestination) {
  const tree = serializer.parse(String(markdown));
  const definitions = firstDefinitionsByIdentifier(tree);
  const usedDefinitions = new Set();
  visit(tree, "linkReference", (node) => {
    if (definitions.has(node.identifier)) usedDefinitions.add(node.identifier);
  });

  visit(tree, "link", (node) => {
    const replacement = resolveDestination(node.url, {
      line: node.position?.start.line ?? 1,
      type: "link",
    });
    if (replacement !== undefined && replacement !== null) {
      node.url = String(replacement);
    }
  });
  for (const identifier of usedDefinitions) {
    const definition = definitions.get(identifier);
    const replacement = resolveDestination(definition.url, {
      line: definition.position?.start.line ?? 1,
      type: "definition",
    });
    if (replacement !== undefined && replacement !== null) {
      definition.url = String(replacement);
    }
  }
  return serializer.stringify(tree);
}

export function markdownStructure(markdown) {
  const headings = [];
  const fences = [];
  const tree = parseMarkdownAst(markdown);
  visit(tree, "heading", (node) => {
    headings.push(node.depth);
  });
  visit(tree, "code", (node) => {
    fences.push({
      info: node.lang ?? "",
      content: node.value,
    });
  });
  return { headings, fences };
}

export function markdownInlineCodeLiterals(markdown) {
  const literals = [];
  const tree = parseMarkdownAst(markdown);
  visit(tree, "inlineCode", (node) => {
    literals.push(node.value.replace(/\s+/gu, " ").trim());
  });
  return literals;
}

export function markdownExternalUrls(markdown) {
  return markdownLinks(markdown)
    .map((link) => link.destination)
    .filter((destination) => /^https?:\/\//iu.test(destination));
}

function collectProse(node, values) {
  if (node.type === "text") {
    values.push(node.value);
    return;
  }
  if (
    node.type === "code" ||
    node.type === "inlineCode" ||
    node.type === "html" ||
    node.type === "definition" ||
    node.type === "image"
  ) {
    return;
  }
  for (const child of node.children ?? []) collectProse(child, values);
}

export function markdownProseText(markdown) {
  const values = [];
  collectProse(parseMarkdownAst(markdown), values);
  return values.join(" ").replace(/\s+/gu, " ").trim();
}
