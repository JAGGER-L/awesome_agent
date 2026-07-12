export function formatStreamingMarkdown(source: string): string {
  return source
    .split("\n")
    .map((line) =>
      line
        .replace(/^\s{0,3}#{1,6}\s+/u, "")
        .replace(/^\s*[-+*]\s+/u, "• ")
        .replace(/^\s*>\s?/u, "│ ")
        .replace(/^\s*```([^\s]*)\s*$/u, "$1"),
    )
    .join("\n")
    .replace(/\*\*([^*\n]+)\*\*/gu, "$1")
    .replace(/__([^_\n]+)__/gu, "$1")
    .replace(/`([^`\n]+)`/gu, "$1")
    .replace(/\*([^*\n]+)\*/gu, "$1")
    .replace(/_([^_\n]+)_/gu, "$1");
}
